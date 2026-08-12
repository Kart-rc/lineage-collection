package shop;

import jakarta.persistence.*;

@Entity
@Table(name = "customers")
public class Customer {
    private Integer id;

    @Column(name = "last_name")
    private String lastName;

    private String city;
}
